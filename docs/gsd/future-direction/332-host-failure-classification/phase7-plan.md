# Forge #332 -- Phase 7: follow-ups after the Phase 6 credit preserve

Status: **wave 1 delivered; wave 2 needs owner decisions**
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

What changes behaviour: W1-1 (outcomes of a `StateMachine` whose `run()` is
called again, reachable only through the Python API), W1-5 (the text, and so
the fingerprint, of one receipt diagnostic) and W1-8 (a crash fix that adds
one optional host row field; its credit outcomes follow #114, stricter in one
case described in 1.2).
The rest is refactoring, comments and tests. Code comments call W1-1 "P7-1".

| # | Item (PR #319 wording) | Summary | Proof |
| --- | --- | --- | --- |
| W1-1 | "clearing the in-memory review state when no matching state file loads (planned for the next phase)" | A later LOCAL run starts where a new instance would; see 1.1. | `test_p7_*` differentials |
| W1-2 | "a public accessor for excerpt rejections" | `_L1Call.excerpt_invocation` and `_L1Call.excerpt_rejections`, read-only, return the stored objects; the machine's five read sites use them. Backing attributes and tamper tests unchanged. | Returning a copy fails the evidence-rejection tests (29 at the time of writing). |
| W1-3 | "moving `_project_product_row` out of `state.py`" | New leaf module `state_rows.py` holds `HOST_ROUND_FIELDS`, `is_host_round`, `_project_product_row`; `state` re-exports them, `state_preserved` imports the projection; both call-time shims are gone. Patching `state._project_product_row` no longer reaches the preserved-row validator. | I-3 pins one shared definition; I-9 that both callers use it. |
| W1-4 | the "this run" comments on advisories and the pre-existing buffer | Comments say which attributes live for the instance and which reset per run. The pre-existing buffer is now reset per run (see 1.1). | Buffer test in 1.1. |
| W1-5 | issue #70, 2026-10-30 comment (the `verify.py` reason it quotes) | The unresolved-finding reason names the basis: "unresolved finding without verified evidence (a product candidate the host could not verify, or an infrastructure failure)", or a malformed / unrecognized basis. Verdict, `failure_kind` and `unresolved_findings` unchanged. One classifier (`verify._basis_kind`) feeds the check and the label. | Label table for every unverified basis shape; the exact reason for the real falsifier producer; the infra-failure-first wording fails 15 tests. |
| W1-6 | "tests or equivalence proofs for the 30 unproven keep-across-runs mutants" | Section 2: 2 more killed by new tests, 1 made equivalent by W1-8, 31 shown equivalent. | Mutant sweep plus per-attribute analysis. |
| W1-7 | "Plan omissions: no schema-fail plus gate-damage row ... and no mixed schema-fail/coverage plus L2-infra row" | Test-only: schema-fail with literal damage on the gate path (both pass orders; full reset); a round with both a schema-fail and an incomplete-coverage finding, with and without an L2 error (preserve), and with a new product finding (reset). The third omission is a plan-wording note, not a test gap. | Dropping the L2-infra arm of the preserve rule fails the mixed L2 case. |
| W1-8 | Found by the W1-6 analysis: host-receipt LOCAL reviews stopped with `CorruptedStateError` on any L2 infrastructure error | See 1.2. | `tests/test_l2_infra_host_rounds.py` |

### 1.1 W1-1: a later run on one instance

A LOCAL `run()` drops the State the previous `run()` on the same instance
used, before it looks for `state.json`, so a missing, other-diff or corrupt
file leaves it where a new instance starts. Only that State object is
dropped: a State the instance was built or seeded with, one a caller assigned
after the previous run, and one no run used (because the run raised before
or inside its state load) are kept. CI is unchanged: it never reloads
`state.json` and keeps its in-memory State (W2-9). Edits made in place on
the State the previous run used are dropped with it; assign a new State to
seed a later run. The pre-existing L0 advisory buffer is reset at every
`run()` entry, so a run that raised before the advisory flush cannot hand its
entries to the next run.

Tests: reused instance vs new instance from the same files, byte-identical
files, over 4 run-1 shapes x {state.json deleted, rewritten for another
diff} (fail on 20b4ace), three more two-run scenarios from section 2, and
boundary cases: seeded State kept by the first run, by a run after one that
raised before or inside the load, and a State assigned between runs; CI
carry-over pinned; the buffer case.

### 1.2 W1-8: L2 infrastructure errors in host-receipt rounds

An L2 infrastructure error (a raised L2 runner or a typed MUTATION_ERROR)
turns CLEAN into INCOMPLETE. In host-receipt mode the round was recorded
`interrupted`, but the round-history validator accepted INCOMPLETE only
beside a falsifier protocol failure, so the machine refused its own row and
the review stopped with `CorruptedStateError` (reproduced on 20b4ace; #114's
tests run without host receipts). Now:

- A host row carries `"l2_infra_error": true` when this round's L2 returned
  an infrastructure error. The validator refuses the field unless it is
  exactly true, L2 returned, and the fixpoint is INCOMPLETE, RESET or
  CYCLE_RESTART.
- A completed round with an L2-only INCOMPLETE is `interrupted`: window kept,
  nothing earned, non-PASS (#114). Three such rounds stop at the mutation
  infrastructure breaker.
- A round that would be CLEAN but for its L2 error gets the same receipt
  acceptance as a CLEAN round (hunk witness, coverage floor, completeness);
  a refusal goes down the receipt-gate path below.
- For a round that cannot prove credit (a failed attempt, a refused receipt
  gate, the survivor breaker) an L2-only INCOMPLETE is `unavailable`, as
  CLEAN is there: only a falsifier protocol stop or an acquisition failure
  interrupts (`StateMachine._interrupts`; the gate and the breaker share
  `_unproved_round_action`; the protocol cause has one definition,
  `_protocol_stop`). The validator accepts `unavailable` + INCOMPLETE only
  with the L2 fact.
- The L2 error is cleared when a host attempt begins (and when the run
  starts and when L2 starts), and the row records it only when this round's
  L2 returned, so no attempt inherits an earlier round's error.
- Stricter than without the L2 error, on purpose: a pure excerpt rejection
  (which alone is `evidence_rejected` and keeps the window) beside an L2
  error is `unavailable`, because `evidence_rejected` requires a CLEAN
  fixpoint. It fails closed; the next run needs a fresh state.
- Older code refuses an `interrupted` or `unavailable` row whose INCOMPLETE
  comes from the L2 error alone, even after the review later reached PASS,
  as it refused these rounds before; it loads the field beside a protocol
  failure, a reset or a preserve and ignores it. A `state.json` that older
  code wrote after such an error (an `interrupted` INCOMPLETE row without
  the field) does not load either: archive `.code-forge/state.json` and
  `.code-forge/receipts/` and start a fresh review. `docs/round-history.md`
  documents the field for users.
- Like every round row, the action word is a consistency record, not an
  authenticated one: editing `unavailable` to `interrupted` on an L2 row
  passes the loader. A local rewrite of state is outside the trust model
  (r5 section 3).

Tests: raised and typed L2 failures keep the window, reload and recover;
the mutation infrastructure breaker; the survivor breaker; a round failing
before L2, and an attempt failing before its round; literal receipt damage,
evidence that receipt acceptance refuses, pure excerpt rejections, and an
acquisition failure (which keeps the window), each beside a healthy, raising
and typed L2; the survivor breaker beside a protocol stop (`interrupted`);
three failures after L2 (crash before publish, receipt-gate read failure,
state save after publish), each with a healthy, raising and typed L2, all
`unavailable` and not restorable; loader cases.

### 1.3 Why W1-5 does not say "infrastructure diagnostic"

A receipt finding carries no `source`, INFRA and UNTRUSTED both get
authority `infra-unavailable`, and an UNTRUSTED finding's file and
description come from the reviewer (a reply can name `<llm-invoke>`).
Labelling on those fields would let a product candidate present itself as
trusted infrastructure, which r5 section 4.3 forbids. A trusted split needs
host facts in the receipt (see W2-6).

## 2. Keep-across-runs mutant sweep (W1-6)

Method: one mutant per reset statement in `StateMachine._reset_run_state`
(44 statements), generated before the L2 infrastructure fix, when the
first-run marker was a run counter. Each guards its statement with a
first-call guard, so `__post_init__` and the first `run()` still initialize
the attribute but a later `run()` on the same instance keeps the earlier
value. (The pre-existing advisory buffer joined `_reset_run_state`
later, with its own test; see 1.1.) Selection:
`test_review_interruption_source_controls.py`,
`test_phase6_schema_coverage_credit.py`, `test_machine_receipt_gate.py`,
`test_rejected_evidence_history.py`, `test_mutation_ledger_ci_controls.py`,
run with `-x`. The generator was a throwaway script and is not committed;
the method above is enough to regenerate it. Results are in section 2.1.

### 2.1 Results

Baseline (unmutated, at the time of the sweep): 955 passed, 1 deselected (the pre-existing
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
  the crash fixed by W1-8, so the error is now also cleared when every host
  attempt begins; together with the row writer's check that this round's L2
  returned, that covers every read.
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
round record `interrupted` (window kept, nothing earned). An L2 infra error
turns CLEAN into INCOMPLETE; a completed round records `interrupted`, but a
failed attempt or a refused receipt gate records `unavailable` (W1-8). A
falsifier protocol failure records `interrupted` with INCOMPLETE on every
path. `unavailable` with an earned window is refused on restore. So the
three causes already differ where the round cannot prove its evidence.

Question for the owner: should they differ further? For example, should a
run of consecutive `interrupted` rounds bound how long a kept window can stay
open, or should a generic transport failure (not typed capacity) reset? And
should a protocol stop or an acquisition failure still keep the window beside
literal receipt damage on the gate path, as they do today? (The protocol case
is pinned by `test_falsifier_protocol_failure_preserves_only_prior_credit` in
`tests/test_resume_receipt_provenance.py`; the acquisition case has no
dedicated test.) The breakers (3 rounds with a failed pass, the mutation
infra breaker) already stop a review that cannot converge, so a credit-side
bound may be redundant.

Recommendation: no further credit change until there is a field case; pin
the acquisition plus literal damage outcome with a test either way. If a
bound is wanted, count consecutive non-earning host rounds in the
row (`non_earning_streak`) and refuse to extend a window past N, enforced by
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

### W2-8. Smaller items carried from PR #319 and this series

- Payload-shape asymmetry in `factories.py` (non-JSON preserves,
  parsed-with-findings resets outside the narrow C3 case, an inadmissible
  dict wipes): the inadmissible-dict wipe is the only path that loses credit
  without a product observation. Option: route it to the same companion
  rule. Needs C3-style tests.
- TD-P6-C3d: stall / `diagnose.py` do not show `preserved`. Report-only;
  can land with W2-2.
- The plan omissions are covered by W1-7.
- Test harness: the reused-vs-new differentials and the host-mode L2 tests
  share private helpers of `tests/test_review_interruption_source_controls.py`.
  Extracting them into a shared test module is mechanical and deferred.
- The cause of an INCOMPLETE fixpoint is derived from instance state where it
  is used (`_protocol_stop`, `_l2_only_incomplete`, the row writer's
  L2-returned check). Returning the cause with the fixpoint would make the
  writer and the readers agree by construction; deferred as a refactor.
- `verify._basis_kind` returns kind strings that index `_UNRESOLVED_LABELS`;
  an enum would remove the (today unreachable) missing-label path.
- The machine still imports `_L1Call` and checks `type(...) is _L1Call`; the
  W1-2 accessors only stop it reading private attributes. Treating `_L1Call`
  as a package-internal producer type is the intended boundary for now.

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
`test_p7_reused_ci_instance_carries_its_state`), or start a CI run
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

- Behaviour changes were checked red first: each new test fails on the code
  before its fix or on an injected defect, as recorded in the commit messages.
- Full suite (`pytest tests`, 453 collected files, 4 shards, pinned ruff
  first on PATH) on the final tree: 169 known failures, the same set that
  fails on untouched 20b4ace in this container. They come from Vertex
  credentials (`test_llm_invoke*`), the git environment
  (`test_conventions_resolver.py`), running as root (`test_contract_flag.py`,
  `test_fast_fail.py`), the gate pytest evidence and waiver tests, three
  projection-isolation pins that also fail on the Phase 6 heads,
  `test_doctor.py` live probes, `test_dogfood.py`, and four known failures
  (`test_deleted_scope_consumers.py` x2,
  `test_exec_falsify.py::...test_timeout_kills_process_group_descendants`,
  `test_mutation_ledger_ci_controls.py::test_ci_null_baseline_deadline_is_a_config_error_before_any_baseline`).
  One more failed once under the 4-shard load:
  `test_report_projection_compat.py::test_paired_fault_free_oracle[ci_pass_stub]`
  (one side of the pair reported one extra advisory). It passes alone and in
  three concurrent reruns, on this tree and on 20b4ace.
- `tests/test_ruff_detection_contract.py` fails only when `ruff` on PATH is
  not the pinned 0.17.3; it passes with the pinned binary first on PATH.
- Lint: `ruff check` and `ruff format --check` (pinned 0.17.3) clean on every
  changed file; `tests/test_provider_capacity_reporting.py` was formatted in
  its own commit (AST unchanged) because a touched file must be left clean.
- Review: repeated rounds of six independent reviews (security and
  correctness, code quality, redundancy and style, blast radius, a general
  code review, and a verify-and-deduplicate pass), until a round found
  nothing left to fix. Confirmed findings are fixed in this series or
  recorded in section 3 (W2-8, W2-9, W2-10).
- Not run: forge's own LLM review cycles (no backend in this environment) and
  native mutmut. The sweep in section 2 is a manual, targeted one.
