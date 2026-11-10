# Bounded publication-contention evidence

This tests-only supplement covers overlapping calls to the production storage publisher. It is not simultaneous complete CLI/MCP host execution, full T17/A06 acceptance, G0/G1 admission, or a replacement for the original 78-test port. #33 and #441 remain open. G04/G09 consumer decisions and broader G08/G10/G12 acceptance remain pending.

## Source and execution identity

The fixed production baseline is merged main `f4c9214a813cd5604ed63462b3268343ed6f800c`. Production files are unchanged. The locally executed `src/code_forge/_advisory_diagnostics.py` SHA-256 is `5e2df5511f384b25f1639424d72eee8ccb3b741ae99048379d05c9faeff798cc`; the new `tests/test_publication_contention.py` SHA-256 is `9666eb6cf4e7d34b9ced5e1694bed1beb17e088a0548764c9628f425788d65e1`.

The production consumer is `DiagnosticCapture.finish` in `_cross_repo_diagnostics.py`, which binds, publishes and validates the result; `present` calls the strict reader. These tests call the storage publisher directly. They do not exercise capture thread affinity, host UUID issuance, source locks, output delivery or MCP transport.

On Linux local overlayfs, Python 3.12.14 / pytest 9.2.0 executed the new module and existing `tests/test_advisory_diagnostics.py`: **320 passed**. The new six nodes also passed in the separate provider-free environment. These are overlapping suites, not 326 distinct tests. Ruff 0.17.3 full-tree checking and formatting passed. No CI was triggered. Local evidence retains commands, stdout/stderr, JUnit, exact collected nodeids, loaded-module paths, source/test hashes, source-mutant diffs and exact restored runs. This document is a bounded summary, not the raw evidence bundle or maintainer acceptance.

## Executed public controls

All node names below belong to `tests/test_publication_contention.py` and are explicitly registered in `scripts/ci_public_test_identities.json`.

- `test_concurrent_distinct_execution_publications_remain_independent`: two calls use the same source/repository/hash/diff/sequence with different execution IDs and distinguishable legal payloads. Both reach the commit boundary after complete exclusive staging. Both publish and strictly reopen their own complete observations. Locator identity, original staged inode, bytes, digest, private modes, final link counts, leaf inventories and absence of every pending name are checked. Peer namespace and independently changed source-hash contexts are rejected.
- `test_concurrent_same_key_refuses_contender_without_replacing_winner`: both calls clear the absent-name precheck. A completes first; its inode and bytes are captured before B attempts its real commit. A must remain unchanged. B is exactly `unknown / commit_uncertain / locator=None / cleanup=complete`, with one commit attempt and no retry or fallback. Direct result assertions precede strict-reader rejection. This intentionally supplied same key is not a natural host identity collision. It differs from the existing sequential `name_conflict` control.
- `test_concurrent_publishers_preserve_foreign_pending_replacement`: after A commits, the fixture replaces only A's staging name with a real private sentinel. The final link keeps A's original inode allocated. Real cleanup preserves the sentinel, reports A cleanup failed, and removes B's own staging. Both final observations remain readable. Sentinel identity and exact bytes are asserted before cleanup statuses; exact residue is observed before the fixture removes only its own sentinel. Existing sequential ownership tests already cover the primitive; this adds cross-writer isolation evidence.
- `test_contention_harness_reports_failures_instead_of_product_unknown[wrapper]`, `[worker]`, and `[barrier]`: injected scheduler RuntimeError, worker SystemExit and broken barrier are surfaced as harness failures after bounded joins. The wrapper case explicitly verifies that production can convert the exception to unknown, but the independent error record still rejects that run as evidence.

Both workers overlap within publication, with deterministic commit scheduling. B waits until the controller snapshots A; there is no simultaneous-kernel-commit claim. Positive runs use the real filesystem, binding, publisher, codec, cleanup and strict reader. Only scheduling and the explicitly described foreign-name fault are injected. Both link and replace wrappers share the same schedule and forward to saved real operations, so the overwrite source mutation cannot bypass coordination.

Barrier/event/join waits are bounded. Ordinary locks and filesystem operations have no universal wallclock bound. Worker BaseException and wrapper failures are retained separately in memory and reported with closed labels, not raw exception text. Gates are released and threads joined in finally; live workers are failures, never successful unknown evidence. A subprocess timeout is a harness failure, not a product cancellation result. These tests do not add a lifecycle or concurrent descriptor-leak qualification claim.

## Source-specific fault controls

Each isolated source copy ran the exact listed node, produced one call-phase assertion failure at the intended oracle, and passed that same node after byte-for-byte restoration. Collection, import, setup and coordination failures were excluded from semantic RED credit.

| Source fault | Exact target test above | Intended semantic RED |
|---|---|---|
| Replace the production descriptor-relative `os.link` commit with `os.replace`, removing its unsupported `follow_symlinks` argument | Same-key contention | `winner inode and bytes changed`, before statuses or stale-locator reads |
| Omit only `_cleanup_temp`'s identity/nonregular guard | Foreign pending replacement | `foreign pending sentinel removed`, before cleanup-status checks |
| Omit only decoder `context != expected` rejection | Distinct execution contention | `DID NOT RAISE` for independently changed source_hash with unchanged namespace |
| Insert `locator = candidate` only in the unreconciled unknown branch | Same-key contention | `unknown leaked a locator`, before validator/reader rejection |

The commit change is a **replacement mutation**, and the locator fault is an **assignment insertion**, not line omissions. The two guard controls are omissions. The overwrite mutant runs only the applicable same-key test: the foreign-pending fixture requires hard-link staging semantics and must not earn overwrite evidence from a missing temporary name. Successful harness checks precede every intended RED oracle. No independent omission credit is claimed for each redundant foreign-final identity/size/digest guard.

## Retained limits

No product defect was demonstrated by these controls. They detect deliberate source defects in isolated copies; production behavior and the #445 immediate diagnostic, #447 exception transport and #452 cleanup-priority boundaries are unchanged. No public API/schema expansion, consumer decision or recovery interface is introduced.

The supported local Linux/private ownership/ACL/path constraints remain unchanged. This execution qualifies overlayfs only, not every eligible filesystem or platform. There is no crash, SIGKILL, power-loss, reboot, network-filesystem, global cleanup, retention, cancellation, hostile same-UID or inode-generation/ABA guarantee. In particular, checking an inode before unlink remains non-atomic against hostile replacement. #68/#400 lifecycle work and Draft PR dispositions are outside this supplement.
