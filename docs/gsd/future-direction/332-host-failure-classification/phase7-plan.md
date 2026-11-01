# Forge #332 -- Phase 7: follow-ups after the Phase 6 credit preserve

Status: **wave 1 delivered on branch `claude/laughing-bohr-y7r65a`; wave 2 needs owner decisions**
Base: `main` 20b4ace (Phase 6 landed as PR #319, head 22d9fa98, merged 2026-10-30)
Scope source: PR #319 "Not in this PR" and "Deferred / tech debt", plus the
2026-10-30 comment on issue #70 (INFRA/UNTRUSTED findings reported as
"unresolved unverified product finding").

This file records what Phase 7 covers, what has landed, and the decisions the
remaining items need. Listing an item here does not schedule or authorize it.
Wave 2 changes credit policy or the persisted round shape, and those are
owner decisions under the architecture (r5 section 4.5: "Granted-policy
changes = separate proposal").

---

## 1. Wave 1: bounded changes

Each item is one or two commits on the branch. W1-1 to W1-7 change no
verdict, credit action or persisted state shape. W1-8 fixes a crash and adds
one optional host row field; its credit outcome is the existing #114 rule.

| # | Item (PR #319 wording) | What changed | Proof |
| --- | --- | --- | --- |
| W1-1 | "clearing the in-memory review state when no matching state file loads (planned for the next phase)" | A LOCAL `run()` on one `StateMachine` after the run that first used the built State drops the State left in memory before it looks for `state.json`, so a missing, other-diff or corrupt file leaves it where a new instance starts. The first run that reaches the state load still uses the State the instance was built or seeded with; a run that raised before the load does not count. CI is unchanged: it never reloads `state.json` and keeps its in-memory State (see W2-9). | Differential tests (`test_p7_*` in `tests/test_review_interruption_source_controls.py`): 4 run-1 shapes x {state.json deleted, rewritten for another diff}, reused instance vs new instance, byte-identical files; 9 of the first 10 fail on 20b4ace, the CI boundary pin passes on both. The failed-before-load case fails with a run counter instead of the flag. |
| W1-2 | "a public accessor for excerpt rejections" | `_L1Call.excerpt_invocation` and `_L1Call.excerpt_rejections`, read-only, return the stored objects; the machine's five read sites use them. Backing attributes and tamper tests unchanged. | New accessor test. Returning a copy from the accessor fails 27 existing evidence-rejection tests. |
| W1-3 | "moving `_project_product_row` out of `state.py`" | New leaf module `state_rows.py` holds `HOST_ROUND_FIELDS`, `is_host_round`, `_project_product_row`. `state` re-exports them; `state_preserved` imports the projection directly. Both call-time shims are gone (the `is_host_round` shim had no caller). | I-3 / I-9 now pin one shared definition across the three modules and that both callers reach it. |
| W1-4 | the "this run" comments on advisories and the pre-existing buffer | Comments in `__post_init__`, `_reset_run_state`, `run()` and the terminal receipt gate now say which attributes live for the instance and which reset per run. The pre-existing buffer is described as it behaves: reset by every L0 phase, flushed on a normal return. No message text changed. | Comment-only. |
| W1-5 | issue #70, 2026-10-30 comment (`verify.py:2180`) | The unresolved-finding reason names what the receipt basis shows: "unresolved finding without verified evidence (a product candidate the host could not verify, or an infrastructure failure)", or a finding with a malformed / unrecognized basis. It no longer calls INFRA diagnostics product findings, and it names a product candidate first, because a candidate whose falsification failed is published as INFRA/UNCERTAIN with the same authority. Verdict, `failure_kind` and `unresolved_findings` are unchanged. The receipt-gate diagnostic fingerprints its message, so a failure recorded before and after this change gets a different fingerprint. | An explicit label table for all 17 unverified basis shapes, and the exact reason for the real falsifier protocol / transport producer; restoring the first wording fails 14 tests. |
| W1-6 | "tests or equivalence proofs for the 30 unproven keep-across-runs mutants" | See section 2: 2 more killed by new tests, 1 made equivalent by W1-8, 31 shown equivalent. | Mutant sweep plus per-attribute analysis. |
| W1-7 | "Plan omissions: no schema-fail plus gate-damage row ... and no mixed schema-fail/coverage plus L2-infra row" | Test-only. The gate-path literal-damage test also runs schema-fail with literal damage (both pass orders; full reset). Two predicate tests cover a round with both a schema-fail and an incomplete-coverage finding, with and without an L2 infra error (preserve), and the same with a new product finding (reset). The third omission, "a rule that is not testable for mixed rounds", is a plan-wording note, not a test gap. | Dropping the L2-infra arm of the preserve rule fails the mixed L2 case. |
| W1-8 | Found by the W1-6 analysis (not in PR #319): host-receipt LOCAL reviews stopped with `CorruptedStateError` on any L2 infrastructure error | The fixpoint turned CLEAN into INCOMPLETE and the round was recorded `interrupted`, but the round-history validator accepted INCOMPLETE only beside a falsifier protocol failure, so the machine refused its own row. #114's tests run without host receipts and never saw it. Host rows now carry `"l2_infra_error": true` when this round's L2 returned an infrastructure error; the validator accepts `interrupted` + INCOMPLETE with it and refuses it on earned / evidence-rejected rows, with a non-true value or without a returned L2. The L2 error is also cleared at the start of every round, so a round that fails before its L2 does not inherit the previous round's error. On the receipt-gate path an L2-only INCOMPLETE does not interrupt: a round whose evidence the gate refused stays `unavailable` (window not restorable, next run fails without a reviewer call), as with a healthy L2; the validator accepts `unavailable` + INCOMPLETE only with the L2 fact. Older code refuses rows with the new field the same way it refused these rounds before. | `tests/test_l2_infra_host_rounds.py` (21 tests); 15 of the first 17 fail without the change, the round-B test fails without only the per-round clear, and the receipt-damage cases fail with the old gate mapping. Reproduced on 20b4ace. |

Why W1-5 does not say "infrastructure diagnostic": a receipt finding carries
no `source`, INFRA and UNTRUSTED both get authority `infra-unavailable`, and
an UNTRUSTED finding's file and description come from the reviewer (a reply
can name `<llm-invoke>`). Labelling on those fields would let a product
candidate present itself as trusted infrastructure, which r5 section 4.3
forbids. A trusted split needs host facts in the receipt (see W2-6).

## 2. Keep-across-runs mutant sweep (W1-6)

Method: one mutant per reset statement in `StateMachine._reset_run_state`
(44 statements), generated at 26bd5af, when the first-run marker was a run
counter. Each guards its statement with `if self._runs_started == 0:`, so
`__post_init__` and the first `run()` still initialize the attribute but a
later `run()` on the same instance keeps the earlier value. Selection:
`test_review_interruption_source_controls.py`,
`test_phase6_schema_coverage_credit.py`, `test_machine_receipt_gate.py`,
`test_rejected_evidence_history.py`, `test_mutation_ledger_ci_controls.py`,
run with `-x`. Results are in section 2.1.

### 2.1 Results

Baseline (unmutated, at 26bd5af): 955 passed, 1 deselected (the pre-existing
`test_ci_null_baseline_deadline_is_a_config_error_before_any_baseline`
failure, which also fails on 20b4ace).

Killed (10): `_round_input_tokens`, `_round_output_tokens`,
`_round_cached_tokens`, `_round_duration` (fix11 l1-timeout differential);
`_host_attempt_round` (fix12 double fault); `_phase_status`, `_phase_findings`
(fix10 early second run); `_settled_preserve_round` (fix9 preflight Ctrl-C);
`_trusted_config_refusal` (fix12 CI refusal); `_untracked_gate_pinned` (fix11).

Survived (34): `_round_pass_usage`, `_written_pass_outcomes`,
`_last_receipt_write_errors`, `_excerpts_last_round`, `_attempted_last_round`,
`_responses_last_round`, `_raw_observations_last_round`,
`_pending_raw_responses_last_round`, `_unavailable_rejected_passes_last_round`,
`_c3_strip_fps`, `_c3_stripped_dispositions`, `_booked_receipt_write_errors`,
`_acquisition_markers`, `_falsify_protocol_findings`, `_falsify_failed_findings`,
`_hold_confirmed_fingerprints`, `_acquisition_receipts`, `_acquisition_authority`,
`_rejection_authority`, `_rejected_evidence_entries`, `_rejection_receipt_digests`,
`_rejection_written_outcomes`, `_attempt_snapshot`, `_attempt_findings`,
`_other_source_state`, `_mutation_bound_failure`, `_mutation_not_applicable`,
`_mutation_workspace_blocker`, `_mutation_timeout_objects`,
`_mutation_timeout_emitted`, `_l2_infra_error`, `_untracked_gate_pin`,
`_trusted_gate_cache`, `_mutation_measurement_eligible`.

A surviving mutant is either equivalent through `run()` (every read in a run
comes after a per-round or per-attempt write that does not use the old value)
or reachable by a two-run scenario no test has yet. Section 2.2 classifies
each one.

### 2.2 Classification of the survivors

Static read of every write and read of each attribute in `machine.py` (no
other module reads them), checked against the killed resets that always
hold in a reused run (`_host_attempt_round`, `_phase_status`,
`_phase_findings`, `_settled_preserve_round`, `_untracked_gate_pinned`).

Killable, now killed by new two-run differentials (2):
- `_last_receipt_write_errors`: run 1 cannot write receipts and fails after
  the round; run 2 fails after L1, before the per-attempt clear. The failed
  path logged run 1's receipt-write error again.
  `test_p7_second_run_failing_after_l1_does_not_relog_run1_receipt_error`.
- `_other_source_state`: run 1 finds another diff's state.json and no
  receipts, saves its own empty State and stops in the preflight; run 2
  continued the other diff's cycle numbers.
  `test_p7_second_run_does_not_continue_cycles_of_run1_other_diff_state`.

Killable before, equivalent now (1):
- `_l2_infra_error`: run 2 failing after L1 and before its own L2 read run
  1's L2 error. The same leak existed between two rounds of one run, and fed
  the crash fixed by W1-8, so the error is now also cleared at the start of
  every round; that clear dominates every read.
  `test_p7_second_run_failing_before_l2_ignores_run1_l2_error` and
  `test_round_failing_before_l2_does_not_inherit_the_previous_round_l2_error`
  pin both cases.

Equivalent with fixed configuration (1):
- `_hold_confirmed_fingerprints`: in host-receipt mode it is rewritten before
  every fixpoint read; outside it, it is never non-empty. It differs only if
  a caller flips `coverage_l1_active` (or the receipt diff) between two runs
  on one instance, which production never does.

Equivalent (30): each is rewritten, from no old value, before any read on
every path through a run. The dominating write is at the top of
`_execute_round` (`_written_pass_outcomes`, `_acquisition_markers`,
`_acquisition_receipts`, `_acquisition_authority`, `_rejection_authority`,
`_rejected_evidence_entries`, `_rejection_receipt_digests`,
`_rejection_written_outcomes`, `_mutation_timeout_emitted`), in the L1 part of
the round before its readers (`_round_pass_usage`, `_excerpts_last_round`,
`_attempted_last_round`, `_responses_last_round`,
`_raw_observations_last_round`, `_pending_raw_responses_last_round`,
`_unavailable_rejected_passes_last_round`, `_booked_receipt_write_errors`,
`_falsify_protocol_findings`, `_falsify_failed_findings`, `_attempt_findings`),
in `_begin_host_attempt` with every reader guarded by an attempt in the same
run (`_c3_strip_fps`, `_c3_stripped_dispositions`, `_attempt_snapshot`), at
the start of `_run_l2_phase` with readers only after L2 or only in CI after
the round (`_mutation_bound_failure`, `_mutation_not_applicable`,
`_mutation_workspace_blocker`, `_mutation_timeout_objects`,
`_mutation_measurement_eligible`), next to `_untracked_gate_pinned`
(`_untracked_gate_pin`), or in `run()` before its only reader
(`_trusted_gate_cache`).

## 3. Wave 2: needs an owner decision

Each item: what happens today, why it matters, options, and a recommendation.
Nothing here is implemented.

### W2-1. TD-P6-C3a -- companions in non-preserved rows seed `ever_seen`

Today: `_append_round_snapshot` writes a disposition for every finding,
UNTRUSTED companions included. Only a `preserved` row has its companion
fingerprints stripped (`_c3_strip_fps`). Reset, interrupted, earned and
legacy rows keep them, so they enter `ever_seen` in `_fixpoint_reached`. A
trusted finding first CONFIRMED later at a companion's fingerprint counts as
recurring and does not trigger clause (a). Pinned by
`test_c3a_reset_row_seeds_ever_seen`.

Options:
- A. Keep (status quo). The gap needs an UNTRUSTED companion and a trusted
  finding with the same fingerprint in a later round.
- B. Writer-side: strip UNTRUSTED fingerprints from every host row's
  `dispositions` and keep them only in `l1_fingerprints` and receipts.
  Changes the stored snapshot of every row; the loader cannot tell a stripped
  fingerprint from one that was never there (same limit as C3b).
- C. Reader-side: persist the UNTRUSTED fingerprint set per row (new optional
  field) and have `ever_seen` skip it. Additive; old rows read as today.

Recommendation: C, together with W2-2, so both land behind one persisted-shape
decision.

### W2-2. Loader tier gap and TD-P6-C3b -- `preserved` rows the loader cannot fully check

Today: the loader accepts a `preserved` row by shape (all phases returned, a
CONFIRMED schema/coverage cause in `l1_fingerprints`, other open dispositions
credit-neutral). Severity is not stored, so it cannot refuse a P0-P2
recurrence, P3 density or distinct-rule overruns, or verify that the
fingerprints missing from the row were companions. Only the machine's
counterfactual checks these.

Option: persist a `preserve_basis` on new `preserved` rows, for example
`{"version": 1, "causes": [...], "companions": [...], "counterfactual":
"CLEAN" | "INCOMPLETE", "recurring": {fp: severity}}`. The loader then checks
that `causes` match the CONFIRMED (iii) dispositions, `companions` are in
`l1_fingerprints` and absent from `dispositions`, every recurring CONFIRMED
fingerprint is listed with a P3 severity, and `INCOMPLETE` appears only with
a recorded L2 infra error.

Decisions needed:
1. Rows written by the Phase 6 head have no basis. Accept them as today
   (the check covers new rows only, so it hardens against accidents, not a
   forger who drops the field), or refuse them (fail closed; affected users
   start from a fresh state and lose the earned window). Phase 6 has been on
   main since 2026-10-30.
2. Can the loader recompute P3 density without the diff line count? If not,
   density stays machine-only and the basis records the computed result.
3. Threat model: r5 section 3 keeps a consistent full-store local rewrite out
   of scope; the basis is a consistency record, not an authentication.

Recommendation: additive basis, legacy rows accepted with a one-line
warning, density recorded as the machine's result. Revisit refusal once no
Phase 6 states remain.

### W2-3. TD-P6-IV -- sibling credit rules

Today: acquisition failures (capacity and generic) on an otherwise CLEAN
round record `interrupted` (window kept, nothing earned); an L2 infra error
alone turns CLEAN into INCOMPLETE; a falsifier protocol failure records
`interrupted` with INCOMPLETE. `unavailable` with an earned window is refused
on load. These three causes are credited alike.

Question for the owner: should they differ? For example, should a run of
consecutive `interrupted` rounds bound how long a preserved window can stay
open, or should a generic transport failure (not typed capacity) reset? The
breakers (3 rounds with a failed pass, the mutation infra breaker) already
stop a review that cannot converge, so a credit-side bound may be redundant.

Recommendation: no credit change until there is a field case. If a bound is
wanted, count consecutive non-earning host rounds in the row
(`non_earning_streak`) and refuse to extend a window past N, enforced by
machine and loader together.

### W2-4. TD-P6-V -- pure excerpt `evidence_rejected` earn/preserve

Today: a round whose only defect is rejected excerpt evidence records
`evidence_rejected` (window kept, nothing earned), with typed per-pass
rejection identities. Phase 6 made the schema-fail companion path refuse
passes that had an excerpt rejection.

Question: may such a round earn credit when the rejected excerpts are only a
repair failure on an otherwise complete, healthy pass? That changes what
counts as evidence and needs its own proposal. Recommendation: keep as is.

### W2-5. TD-P6-VI / WP-X3 -- per-file coverage gap credit

Today: an incomplete-coverage finding is per pass (`incomplete-coverage-<pass>`),
not per file, and preserves or resets as a whole. A per-file policy would let
a round that missed only coverage-exempt or unchanged files keep credit.
Needs the coverage guard to report per file, a persisted per-file record, and
a loader rule. Recommendation: defer until coverage findings carry per-file
host facts; otherwise the loader cannot check the rule.

### W2-6. WP-R -- receipt-subtype credit labels; W1-5 follow-up

Today: receipt gate failures are not labelled by subtype (`RECEIPT_SCHEMA`,
`RECEIPT_COVERAGE`, ...). The same missing host fact blocks a trusted
INFRA/UNTRUSTED split in `verify.py` (W1-5). A host-written `source` (or
`channel`) on each receipt finding, covered by the existing receipt digest,
would let verify and the report tell them apart without trusting reviewer
text. This is a receipt-format change (docs/receipt-format.md) and needs an
old-reader decision (r5 section 4.8).

### W2-7. WP-M -- persistent modifiers / channel cache

r5 section 5 defers it unless a phase proves the need. Nothing in Phase 6 or
wave 1 does. Recommendation: drop from Phase 7.

### W2-8. Smaller items carried from PR #319

- Payload-shape asymmetry in `factories.py` (non-JSON preserves,
  parsed-with-findings resets outside the narrow C3 case, an inadmissible
  dict wipes): the inadmissible-dict wipe is the only path that loses credit
  without a product observation. Option: route it to the same companion
  rule. Needs C3-style tests.
- TD-P6-C3d: stall / `diagnose.py` do not show `preserved`. Report-only;
  can land with W2-2.
- The plan omissions are covered by W1-7.

### W2-9. A CI run on an instance that ran before keeps that State

Today (unchanged by W1-1): CI never reloads `state.json`, and a later CI
`run()` on the same instance keeps the in-memory State. Existing CI tests
rely on this to stage prior dispositions
(`test_ci_fresh_survivor_excludes_prior_disposition_o4` and others). It also
means a LOCAL run that loaded a matching `state.json`, human DISMISSED
dispositions included, followed by a CI run on the same instance, carries
those dispositions into CI, which the STATE-09 docstring says CI avoids.
Production builds one instance per review, so this is reachable only through
the Python API. Options: leave it (W1-1 pins CI carry-over with
`test_p7_reused_ci_instance_still_carries_its_state`), or start a CI run
empty whenever an earlier run on the instance ran, and move the CI tests to
seed State explicitly. Recommendation: the second, as its own change, because
it rewrites how those CI tests stage history.

### W2-10. The mutation-infra breaker demotes a `preserved` third round

PR #319 accepted that any failure after a round settled `preserved` in the
same attempt demotes it to `reset`, and named the mutation-infra breaker
among them. In host-receipt mode that case was unreachable until W1-8,
because the first L2 infra round crashed. It is reachable now: two L2-infra
rounds (`interrupted`, window kept), then a third with an L2 error and an
incomplete-coverage finding settles `preserved`, the breaker raises after
the save, and the failed-attempt path demotes the row and wipes the window.
Three plain L2-infra rounds keep the window. Question: should a deliberate
breaker stop keep a preserve its round already settled? Recommendation:
yes, by excluding `TimeoutBreaker` raised by
`_check_mutation_infra_can_still_converge` after the final save from the
demotion, with a test for both outcomes; it is a credit-policy change, so it
waits for the owner.

## 4. Verification record (wave 1)

- Each commit message names the tests it adds and the defect injection that
  makes them fail.
- Full suite (`pytest tests`, 30291 collected, 4 shards, pinned ruff first
  on PATH) at 356b759: 29967 passed, 169 failed. Every one of the 169 fails
  the same way on untouched 20b4ace, re-run in its own worktree. They are
  environmental in this container: Vertex credentials (`test_llm_invoke*`),
  the git environment (`test_conventions_resolver.py`), running as root
  (`test_contract_flag.py`, `test_fast_fail.py`), the gate pytest evidence and
  waiver tests, three projection-isolation pins that also fail on the Phase 6
  heads, `test_doctor.py` live probes, `test_dogfood.py`, and the four known
  failures (`test_deleted_scope_consumers.py` x2,
  `test_exec_falsify.py::...test_timeout_kills_process_group_descendants`,
  `test_mutation_ledger_ci_controls.py::test_ci_null_baseline_deadline_is_a_config_error_before_any_baseline`).
- The last commit (f5a695e, receipt damage beside an L2 error) came after
  that run: the 11 neighbouring files (2560 tests) pass on it.
- `tests/test_ruff_detection_contract.py` fails only when `ruff` on PATH is
  not the pinned 0.17.3; it passes with the pinned binary first on PATH.
- Lint: `ruff check` and `ruff format --check` (pinned 0.17.3) clean on every
  changed file; `tests/test_provider_capacity_reporting.py` was formatted in
  its own commit (AST unchanged) because a touched file must be left clean.
- Independent review: two read-only review passes over the behavior
  commits. Findings acted on: the first verify wording discounted product
  candidates whose falsification failed (fixed in 2413865); an L2 error beside
  receipt damage kept credit (fixed in f5a695e); a run that failed before the
  state load consumed the seeded State (fixed in ad39ff7). Recorded, not
  changed: W2-9, W2-10.
- Not run: forge's own LLM review cycles (no backend in this environment) and
  native mutmut. The sweep in section 2 is a manual, targeted one.
