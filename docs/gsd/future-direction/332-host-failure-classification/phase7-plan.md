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

## 1. Wave 1: bounded, no credit-policy change

Each item is one commit on the branch. None changes a verdict, a credit
action or the persisted state shape.

| # | Item (PR #319 wording) | What changed | Proof |
| --- | --- | --- | --- |
| W1-1 | "clearing the in-memory review state when no matching state file loads (planned for the next phase)" | A LOCAL `run()` after the first on one `StateMachine` drops the State the earlier run left in memory before it looks for `state.json`. The first `run()` still starts from the State the instance was built or seeded with. CI is unchanged: it never reloads `state.json` and keeps its in-memory State (pinned by `test_mutation_ledger_ci_controls.py`). | New differential tests (`test_p7_*` in `tests/test_review_interruption_source_controls.py`): 4 run-1 shapes x {state.json deleted, rewritten for another diff}, reused instance vs new instance, byte-identical files. 9 of 10 fail on 20b4ace; the CI boundary pin passes on both. |
| W1-2 | "a public accessor for excerpt rejections" | `_L1Call.excerpt_invocation` and `_L1Call.excerpt_rejections`, read-only, return the stored objects; the machine's five read sites use them. Backing attributes and tamper tests unchanged. | New accessor test. Returning a copy from the accessor fails 27 existing evidence-rejection tests. |
| W1-3 | "moving `_project_product_row` out of `state.py`" | New leaf module `state_rows.py` holds `HOST_ROUND_FIELDS`, `is_host_round`, `_project_product_row`. `state` re-exports them; `state_preserved` imports the projection directly. Both call-time shims are gone (the `is_host_round` shim had no caller). | I-3 / I-9 now pin one shared definition across the three modules and that both callers reach it. |
| W1-4 | the "this run" comments on advisories and the pre-existing buffer | Comments in `__post_init__`, `_reset_run_state`, `run()` and the terminal receipt gate now say which attributes live for the instance and which reset per run. The pre-existing buffer is described as it behaves: reset by every L0 phase, flushed on a normal return. No message text changed. | Comment-only. |
| W1-5 | issue #70, 2026-10-30 comment (`verify.py:2180`) | The unresolved-finding reason names what the receipt basis shows: "unresolved finding without verified evidence (an infrastructure failure, or a product candidate in untrusted reviewer output)", or a malformed / unrecognized basis. It no longer calls these product findings. Verdict, `failure_kind` and `unresolved_findings` are unchanged. | Label asserted for all 17 unverified basis shapes; restoring the old wording fails 13 tests. |
| W1-6 | "tests or equivalence proofs for the 30 unproven keep-across-runs mutants" | See section 2. | Mutant sweep. |
| W1-7 | "Plan omissions: no schema-fail plus gate-damage row ... and no mixed schema-fail/coverage plus L2-infra row" | Test-only. The gate-path literal-damage test also runs schema-fail with literal damage (both pass orders; full reset). Two predicate tests cover a round with both a schema-fail and an incomplete-coverage finding, with and without an L2 infra error (preserve), and the same with a new product finding (reset). The third omission, "a rule that is not testable for mixed rounds", is a plan-wording note, not a test gap. | Dropping the L2-infra arm of the preserve rule fails the mixed L2 case. |

Why W1-5 does not say "infrastructure diagnostic": a receipt finding carries
no `source`, INFRA and UNTRUSTED both get authority `infra-unavailable`, and
an UNTRUSTED finding's file and description come from the reviewer (a reply
can name `<llm-invoke>`). Labelling on those fields would let a product
candidate present itself as trusted infrastructure, which r5 section 4.3
forbids. A trusted split needs host facts in the receipt (see W2-6).

## 2. Keep-across-runs mutant sweep (W1-6)

Method: one mutant per reset statement in `StateMachine._reset_run_state`
(44 statements). Each guards its statement with `if self._runs_started == 0:`,
so `__post_init__` and the first `run()` still initialize the attribute but a
later `run()` on the same instance keeps the earlier value. Selection:
`test_review_interruption_source_controls.py`,
`test_phase6_schema_coverage_credit.py`, `test_machine_receipt_gate.py`,
`test_rejected_evidence_history.py`, `test_mutation_ledger_ci_controls.py`,
run with `-x`. Results are in section 2.1.

### 2.1 Results

Pending: the sweep was still running when this file was first committed.
The results replace this paragraph in a later commit.

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

## 4. Verification record (wave 1)

- Targeted suites per commit, listed in each commit message.
- All test files that build a `StateMachine` (101 files, 16362 tests) on the
  W1-1 tree: the only failures are 4 tests that also fail on untouched
  20b4ace (`test_deleted_scope_consumers.py` x2,
  `test_exec_falsify.py::...test_timeout_kills_process_group_descendants`,
  `test_mutation_ledger_ci_controls.py::test_ci_null_baseline_deadline_is_a_config_error_before_any_baseline`)
  and the ruff detection contract, which fails only when `ruff` on PATH is
  not the pinned 0.17.3 (it passes with the pinned binary first on PATH).
- Lint: `ruff check` and `ruff format --check` (pinned 0.17.3) clean on every
  changed file; `tests/test_provider_capacity_reporting.py` was formatted in
  its own commit (AST unchanged) because a touched file must be left clean.
- Not run: forge's own LLM review cycles (no backend in this environment) and
  native mutmut. The mutant sweep in section 2 is a manual, targeted one.
