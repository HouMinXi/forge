# Phase 25-05 Summary

**Wave 3 (continued): two-repo integration tests**
**Commits:** 32bb8f6, 4708691 (merged to main via ff from phase25-wave3b)
**Date:** 2026-06-20

> Reconstructed post-hoc from the merged commits. Same approach as the
> 25-01..04 SUMMARYs that were also written after the code landed.

## What changed

tests/test_cross_repo.py: added 9 integration test functions (44 pytest cases
  total, up from 35) that drive the real run_cross_repo orchestrator over two
  ephemeral git repos created via tmp_path.  Tests cover: joint context
  assembly with both repo headings (positive + cross-contamination negative
  assertions), primary-authoritative verdict merge (primary PASS + sibling
  FAIL = joint PASS with advisory warning), per-repo source-file plumbing
  (non-empty absolute paths handed to StateMachine), receipt label-prefixing
  (primary-receipt-c*.json and sibling-receipt-c*.json on disk), fail-closed
  error propagation on bad sibling ref, the is_primary fork (spy proves
  build_l1_provider called once with joint diff for primary only), and the
  ESCALATED verdict advisory warning path.

  Also hardened the pre-existing _CrashOnSecond mock (renamed _CrashOnSibling):
  replaced a counter-based thread-scheduling-dependent crash with label-based
  deterministic dispatch via baseline_spec_repr.  All mock StateMachines now
  assert their expected kwargs are present, surfacing contract drift as a
  loud failure instead of a silent wrong-verdict.

## Must-haves verified (against merged code)

- Two real git repos declared as siblings produce a joint review context
  containing both diffs: YES (test_joint_context_contains_both_diffs)
- A finding in the sibling repo appears in the joint output: YES
  (test_findings_attributed_label, with negative cross-contamination guard)
- A sibling with an invalid ref causes the review to fail-closed: YES
  (test_invalid_sibling_ref_fails_closed, bug-injection proven)
- The joint verdict for primary-PASS + sibling-FAIL is PASS with advisory
  warning emitted: YES (test_run_cross_repo_primary_determines_verdict,
  bug-injection proven)
- Per-repo receipts are written with the correct label-prefixed filenames: YES
  (test_receipt_naming_primary, real StateMachine + stub engine)
- Primary thread receives joint diff for L1; siblings do not call
  build_l1_provider: YES (test_primary_receives_joint_diff_as_l1_context,
  bug-injection proven)
- ESCALATED sibling verdict triggers advisory warning: YES
  (test_sibling_escalated_verdict_triggers_advisory_warning, bug-injection proven)

## Review

mimo-pro external backend: 4 rounds (converged from 7 real findings to 3 repeat
concerns). Inline review: 1 round (found cross-contamination coverage gap that
mimo-pro missed). All findings fixed across 4 iterative commits, then squashed to
2 clean commits before merge.
